package tema2.t03;

import java.util.Scanner;

public class ej02 {
    public static void main(String[]args){
        Scanner sc=new Scanner(System.in);

        System.out.println("Digite un numero:");
        int a = sc.nextInt();
        System.out.println("Digite otro numero:");
        int b = sc.nextInt();
        sc.close();
        int suma = sumar(a,b);
        System.out.println("Suma: " + suma);
    }
    static int sumar(int a, int b){
        return a+b;
    }
}
