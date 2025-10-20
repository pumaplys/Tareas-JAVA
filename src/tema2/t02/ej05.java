package tema2.t02;

import java.util.Scanner;

// Desarrolla un programa que solicite un número n y luego muestre por pantalla la siguiente figura:
//1
//
//12
//
  //      123
//
  //      1234
//
    //    12345
//
      //  …………
//
   //      1 2 3 4 5 …..n
public class ej05 {
    public static void main(String[] args) {
        Scanner sc=new Scanner(System.in);

        System.out.println("Digite un numero");
        int num = sc.nextInt();

        for (int i = 1; i <= num; i++){
            for (int j = 1; j <= i; j++){
                System.out.print(j);
            }
            System.out.println();
        }
    }
}
