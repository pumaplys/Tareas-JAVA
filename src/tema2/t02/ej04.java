package tema2.t02;

import java.util.Scanner;

public class ej04 {
    public static void main(String[] args) {
        Scanner sc=new Scanner(System.in);

        int contador = 0;

        System.out.println("Digite un numero");
        int num = sc.nextInt();

        if (num <= 0){
            System.out.println(num + " tiene una sola cifra");
        } else{
            while (num > 0){
                num = num / 10;
                contador++;
            }
        }
        System.out.println("el numero tiene " + contador + " cifras");
    }
}
