package tema1.t03;

import java.util.Scanner;

public class ej16 {
    public static  void main(String args[]){
        Scanner sc=new Scanner(System.in);

        System.out.print("Introduce un número para mostrar su tabla de multiplicar: ");
        int numero = sc.nextInt();

        System.out.println("Tabla de multiplicar del " + numero + ":");

        System.out.println(numero + " x 1 = " + (numero * 1));
        System.out.println(numero + " x 2 = " + (numero * 2));
        System.out.println(numero + " x 3 = " + (numero * 3));
        System.out.println(numero + " x 4 = " + (numero * 4));
        System.out.println(numero + " x 5 = " + (numero * 5));
        System.out.println(numero + " x 6 = " + (numero * 6));
        System.out.println(numero + " x 7 = " + (numero * 7));
        System.out.println(numero + " x 8 = " + (numero * 8));
        System.out.println(numero + " x 9 = " + (numero * 9));
        System.out.println(numero + " x 10 = " + (numero * 10));
    }
}
